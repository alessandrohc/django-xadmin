jQuery(function($){
    $('.diff_field').each(function(){
        var el = $(this);
        var textarea = el.find('textarea.org-data');
        var title = el.data('org-data') || el.attr('title');
        if(textarea.length){
            title = textarea.val();
        }
        el.find('.controls').tooltip({
            title: title,
            html: true
        })
    });

    $('.formset-content .formset-row').each(function(){
        var row = $(this);
        var del = row.find('input[id $= "-DELETE"]');
        if(del.val() == 'on' || del.val() == 'True'){
            row.addClass('row-deleted');
            del.val('on');
        }
        var idinput = row.find('input[id $= "-id"]');
        if(idinput.val() == '' || idinput.val() == undefined){
            row.addClass('row-added');
            row.find('.formset-num').html(gettext('New Item'));
        }
    });

    $('.js-control-diff-formset-model').each(function () {
        var $model = $(this);
        var $model_instance = $model.next();
        while (($model_instance.length > 0) && ($model_instance.hasClass('js-control-diff-formset-instance'))) {
            if ($model_instance.find('.diff-row').length > 0) {
                $model.addClass('has-diff-row');
                break; // when find the first 'diff'.
            }
            $model_instance = $model_instance.next();
        }
    });
});